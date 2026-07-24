<script setup>
import { computed } from 'vue'
import Icon from '@/components/ui/Icon.vue'
import { imgProxy, imgThumb } from '@/lib/img'

const props = defineProps({ manga: { type: Object, required: true }, score: { type: Number, default: null } })
defineEmits(['open'])
const thumb = computed(() => imgThumb(props.manga.cover))
const tier = computed(() => {
  const s = props.score
  if (!s) return ''
  if (s >= 75) return 'is-high'
  if (s >= 60) return 'is-mid'
  return 'is-low'
})
</script>

<template>
  <article class="mc" tabindex="0" @click="$emit('open', manga)" @keydown.enter="$emit('open', manga)">
    <div class="mc__poster">
      <!-- imgProxy: el hotlink directo a uploads.mangadex.org devuelve el
           placeholder anti-hotlink en WebKitGTK (la app de escritorio) -->
      <img v-if="thumb" :src="thumb" class="blurup" aria-hidden="true" alt="" />
      <img v-if="manga.cover" :src="imgProxy(manga.cover, 260)" :alt="manga.title" loading="lazy" @load="$event.target.classList.add('is-loaded')" class="mc__img" />
      <div v-else class="mc__ph"><Icon name="library" :size="28" /></div>
      <div class="mc__scrim" />
      <span class="mc__shine" />
      <span v-if="score" class="mc__score" :class="tier">★ {{ (score / 10).toFixed(1) }}</span>
      <span v-if="manga.contentRating && manga.contentRating !== 'safe'" class="mc__rating">{{ manga.contentRating === 'suggestive' ? '16+' : '18+' }}</span>
      <div class="mc__hover"><span class="mc__btn"><Icon name="search" :size="16" /></span></div>
      <div class="mc__overlay">
        <h3 class="mc__title">{{ manga.title }}</h3>
        <span v-if="manga.year" class="mc__year">{{ manga.year }}</span>
      </div>
    </div>
  </article>
</template>

<style scoped>
.mc { outline: none; cursor: pointer; transition: transform var(--t-base) var(--ease-snap); }
.mc:hover, .mc:focus-visible { transform: translateY(-6px); }
.mc__poster { position: relative; aspect-ratio: 2/3; border-radius: var(--r-md); overflow: hidden; background: var(--surface-2); border: 1px solid var(--line); box-shadow: var(--shadow-sm); transition: box-shadow var(--t-base), border-color var(--t-base); }
.mc:hover .mc__poster { border-color: var(--azure-glow); box-shadow: var(--shadow-lg), 0 0 0 1px var(--azure-glow); }
.mc__img { width: 100%; height: 100%; object-fit: cover; opacity: 0; transition: opacity var(--t-slow), transform var(--t-cine) var(--ease-silk); }
.mc__img.is-loaded { opacity: 1; }
.mc:hover .mc__img { transform: scale(1.07); }
.mc__ph { position: absolute; inset: 0; display: grid; place-items: center; color: var(--ink-ghost); }
.mc__scrim { position: absolute; inset: 0; background: linear-gradient(180deg, rgba(7,10,18,.3) 0%, transparent 30%, transparent 50%, rgba(5,7,13,.95) 100%); }
.mc__shine { position: absolute; inset: 0; pointer-events: none; background: linear-gradient(112deg, transparent 35%, rgba(168,200,255,.14) 48%, transparent 60%); transform: translateX(-120%); }
.mc:hover .mc__shine { animation: shine .8s var(--ease-silk) forwards; }
@keyframes shine { to { transform: translateX(120%); } }
.mc__score { position: absolute; top: var(--s-2); left: var(--s-2); font-size: var(--fs-2xs); font-weight: 700; padding: 2px 0.5rem; border-radius: var(--r-pill); backdrop-filter: blur(6px); background: rgba(7,10,18,.6); color: var(--ink-soft); }
.mc__score.is-high { color: var(--jade); } .mc__score.is-mid { color: var(--gold); } .mc__score.is-low { color: var(--ink-faint); }
.mc__rating { position: absolute; top: var(--s-2); right: var(--s-2); font-family: var(--font-mono); font-size: 0.5625rem; font-weight: 700; padding: 2px 0.375rem; border-radius: var(--r-xs); color: var(--coral); background: rgba(7,10,18,.6); backdrop-filter: blur(6px); }
.mc__hover { position: absolute; inset: 0; display: grid; place-items: center; opacity: 0; transition: opacity var(--t-base); }
.mc:hover .mc__hover, .mc:focus-visible .mc__hover { opacity: 1; }
.mc__btn { width: 2.875rem; height: 2.875rem; display: grid; place-items: center; border-radius: 50%; color: #fff; background: var(--azure); box-shadow: var(--glow-azure); transform: scale(.8); transition: transform var(--t-base) var(--ease-snap); }
.mc:hover .mc__btn { transform: scale(1); }
.mc__overlay { position: absolute; left: 0; right: 0; bottom: 0; padding: var(--s-3); }
.mc__title { font-size: var(--fs-sm); font-weight: 600; color: #fff; line-height: var(--lh-snug); text-shadow: 0 1px 6px rgba(0,0,0,.65); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.mc__year { font-size: var(--fs-2xs); color: var(--ink-soft); text-shadow: 0 1px 4px rgba(0,0,0,.6); }
</style>
