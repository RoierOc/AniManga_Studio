<script setup>
import { computed } from 'vue'
import { useAnimeStore } from '@/stores/anime'
import { animeFormatLabel } from '@/lib/anime'
import Icon from '@/components/ui/Icon.vue'

const props = defineProps({ anime: { type: Object, required: true } })
const store = useAnimeStore()
const inLib = computed(() => store.isInLibrary(props.anime))
const airing = computed(() => props.anime.status === 'RELEASING' || props.anime.next_episode)
</script>

<template>
  <article class="dc" tabindex="0">
    <div class="dc__poster">
      <img v-if="anime.cover" :src="anime.cover" :alt="anime.title" loading="lazy"
           @load="$event.target.classList.add('is-loaded')" class="dc__img" />
      <div class="dc__scrim" />
      <span class="dc__shine" />

      <span class="dc__fmt">{{ animeFormatLabel(anime.format) }}</span>
      <span v-if="anime.score" class="dc__score"><Icon name="spark" :size="10" /> {{ anime.score }}</span>
      <span v-if="airing" class="dc__airing">EN EMISIÓN</span>

      <div class="dc__overlay">
        <h3 class="dc__title">{{ anime.title }}</h3>
        <div class="dc__sub">
          <span>{{ anime.episodes ? anime.episodes + ' ep.' : '? ep.' }}</span>
          <span v-if="anime.genres?.length" class="dc__genres">{{ anime.genres.slice(0, 2).join(' · ') }}</span>
        </div>
        <button class="dc__add" :class="{ 'is-in': inLib }" :disabled="inLib" @click.stop="store.addToLibrary(anime)">
          <Icon :name="inLib ? 'check' : 'spark'" :size="13" />
          {{ inLib ? 'En Mi Anime' : 'Añadir' }}
        </button>
      </div>
    </div>
  </article>
</template>

<style scoped>
.dc { outline: none; transition: transform var(--t-base) var(--ease-snap); }
.dc:hover, .dc:focus-visible { transform: translateY(-6px); }
.dc__poster { position: relative; aspect-ratio: 2/3; border-radius: var(--r-md); overflow: hidden; background: var(--surface-2); border: 1px solid var(--line); box-shadow: var(--shadow-sm); transition: box-shadow var(--t-base), border-color var(--t-base); }
.dc:hover .dc__poster { border-color: var(--azure-glow); box-shadow: var(--shadow-lg), 0 0 0 1px var(--azure-glow); }
.dc__img { width: 100%; height: 100%; object-fit: cover; opacity: 0; transition: opacity var(--t-slow), transform var(--t-cine) var(--ease-silk); }
.dc__img.is-loaded { opacity: 1; }
.dc:hover .dc__img { transform: scale(1.07); }
.dc__scrim { position: absolute; inset: 0; background: linear-gradient(180deg, rgba(7,10,18,.35) 0%, transparent 30%, transparent 45%, rgba(5,7,13,.96) 100%); }
.dc__shine { position: absolute; inset: 0; pointer-events: none; background: linear-gradient(112deg, transparent 35%, rgba(168,200,255,.14) 48%, transparent 60%); transform: translateX(-120%); }
.dc:hover .dc__shine { animation: shine .8s var(--ease-silk) forwards; }
@keyframes shine { to { transform: translateX(120%); } }

.dc__fmt { position: absolute; top: var(--s-2); left: var(--s-2); font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 600; padding: 2px 7px; border-radius: var(--r-xs); background: rgba(7,10,18,.6); backdrop-filter: blur(6px); }
.dc__score { position: absolute; top: var(--s-2); right: var(--s-2); display: inline-flex; align-items: center; gap: 3px; font-size: var(--fs-2xs); font-weight: 700; padding: 2px 7px; border-radius: var(--r-pill); color: var(--gold); background: rgba(7,10,18,.6); backdrop-filter: blur(6px); }
.dc__airing { position: absolute; top: 34px; right: var(--s-2); font-family: var(--font-mono); font-size: 8px; font-weight: 700; letter-spacing: .1em; padding: 2px 6px; border-radius: var(--r-xs); color: var(--cyan); background: var(--cyan-glow); }

.dc__overlay { position: absolute; left: 0; right: 0; bottom: 0; padding: var(--s-3); }
.dc__title { font-size: var(--fs-sm); font-weight: 600; line-height: var(--lh-snug); color: #fff; text-shadow: 0 1px 6px rgba(0,0,0,.65); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.dc__sub { display: flex; flex-direction: column; font-size: var(--fs-2xs); color: var(--ink-soft); margin: 4px 0 var(--s-2); text-shadow: 0 1px 4px rgba(0,0,0,.6); }
.dc__genres { color: var(--ink-faint); }
.dc__add { display: inline-flex; align-items: center; gap: 5px; width: 100%; justify-content: center; padding: 6px; border-radius: var(--r-sm); font-size: var(--fs-xs); font-weight: 600; color: #fff; background: var(--azure); transition: background var(--t-fast); }
.dc__add:hover { background: var(--azure-bright); }
.dc__add.is-in { background: transparent; color: var(--jade); border: 1px solid color-mix(in srgb, var(--jade) 35%, transparent); cursor: default; }
</style>
