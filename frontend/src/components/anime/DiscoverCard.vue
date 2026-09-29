<script setup>
import { computed } from 'vue'
import { useAnimeStore } from '@/stores/anime'
import { imgProxy, imgThumb } from '@/lib/img'
import { useBoxWidth } from '@/lib/useBoxWidth'
import Icon from '@/components/ui/Icon.vue'
import { genero } from '@/lib/etiquetas'

const props = defineProps({ anime: { type: Object, required: true } })
const store = useAnimeStore()
const thumb = computed(() => imgThumb(props.anime.cover))
/* La portada iba DIRECTA al CDN de AniList: sin caché en disco, sin escalera de tamaños.
   MEDIDO en Temporada: 78 peticiones y 1,75 MB a internet en cada visita, con la imagen de 230 px
   pintada a 298 px CSS. Por el proxy sale de disco y al peldaño que mide la caja. */
const [cov, boxW] = useBoxWidth(230)
const coverUrl = computed(() => imgProxy(props.anime.cover, boxW.value))
const inLib = computed(() => store.isInLibrary(props.anime))
const scoreTier = computed(() => {
  const s = props.anime.score
  if (!s) return ''
  if (s >= 75) return 'is-high'
  if (s >= 60) return 'is-mid'
  return 'is-low'
})
</script>

<template>
  <article class="sc" tabindex="0" @click="store.openTorrents(anime)" @keydown.enter.self="store.openTorrents(anime)">
    <div class="sc__cover" ref="cov">
      <img v-if="thumb" :src="thumb" class="blurup" aria-hidden="true" alt="" />
      <img v-if="anime.cover" :src="coverUrl" :alt="anime.title" loading="lazy" decoding="async"
           @load="$event.target.classList.add('is-loaded')" class="sc__img" />
      <div v-else class="sc__ph"><Icon name="film" :size="30" /></div>
      <span class="sc__shine" />

      <span v-if="anime.score" class="sc__score" :class="scoreTier">★ {{ (anime.score / 10).toFixed(1) }}</span>
      <span v-if="anime.status === 'RELEASING'" class="sc__airing">EN EMISIÓN</span>

      <div class="sc__overlay">
        <button class="sc__add" :class="{ 'is-in': inLib }" :disabled="inLib" @click.stop="store.addToLibrary(anime)">
          {{ inLib ? '✓ En Mi Anime' : '+ Mi Anime' }}
        </button>
      </div>
    </div>

    <div class="sc__info">
      <div class="sc__title">{{ anime.title }}</div>
      <div class="sc__meta">
        <!-- El separador va con el dato ANTERIOR, nunca cosido al que le sigue: si AniList no
             da `episodes` (pasa con obras recién anunciadas), un `· EP 6` deja el punto colgando
             al principio de la línea. -->
        <span v-if="anime.episodes" class="sc__eps">{{ anime.episodes }} ep</span>
        <span v-if="anime.next_episode" class="sc__next"><template v-if="anime.episodes">· </template>EP {{ anime.next_episode }}</span>
      </div>
      <div v-if="anime.genres?.length" class="sc__genres">
        <span v-for="(g, i) in anime.genres.slice(0, 3)" :key="g" class="sc__gtag">{{ i ? ' · ' : '' }}{{ genero(g) }}</span>
      </div>
    </div>
  </article>
</template>

<style scoped>
.sc { outline: none; cursor: pointer; transition: transform var(--t-base) var(--ease-snap); }
.sc:hover, .sc:focus-visible, .sc:focus-within { transform: translateY(-6px); }

.sc__cover { position: relative; aspect-ratio: 2/3; border-radius: var(--r-md); overflow: hidden; background: var(--surface-2); border: 1px solid var(--line); box-shadow: var(--shadow-sm); transition: box-shadow var(--t-base), border-color var(--t-base); }
.sc:hover .sc__cover, .sc:focus-within .sc__cover { border-color: var(--azure-glow); box-shadow: var(--shadow-lg), 0 0 0 1px var(--azure-glow); }
.sc__img { width: 100%; height: 100%; object-fit: cover; opacity: 0; transition: opacity var(--t-slow), transform var(--t-cine) var(--ease-silk); }
.sc__img.is-loaded { opacity: 1; }
.sc:hover .sc__img { transform: scale(1.07); }
.sc__ph { position: absolute; inset: 0; display: grid; place-items: center; color: var(--ink-ghost); }
.sc__shine { position: absolute; inset: 0; pointer-events: none; background: linear-gradient(112deg, transparent 35%, rgba(168,200,255,.14) 48%, transparent 60%); transform: translateX(-120%); }
.sc:hover .sc__shine { animation: shine .8s var(--ease-silk) forwards; }
@keyframes shine { to { transform: translateX(120%); } }

.sc__score { position: absolute; top: var(--s-2); left: var(--s-2); font-size: var(--fs-2xs); font-weight: 700; padding: 2px 0.5rem; border-radius: var(--r-pill); backdrop-filter: blur(6px); background: rgba(7,10,18,.6); color: var(--ink-soft); }
.sc__score.is-high { color: var(--jade); }
.sc__score.is-mid { color: var(--gold); }
.sc__score.is-low { color: var(--ink-faint); }
/* Cian sobre `--cyan-glow` (cian translúcido) es ilegible en cuanto el arte es claro — medido
   sobre cielos y fondos rosas de Verano 2026. Mismo fondo opaco que la nota: el color lo pone el
   texto, no el fondo. */
.sc__airing { position: absolute; top: var(--s-2); right: var(--s-2); font-family: var(--font-mono); font-size: 0.5rem; font-weight: 700; letter-spacing: .1em; padding: 2px 0.375rem; border-radius: var(--r-xs); color: var(--cyan); background: rgba(7,10,18,.66); backdrop-filter: blur(6px); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--cyan) 28%, transparent); }

.sc__overlay { position: absolute; inset: 0; display: flex; align-items: flex-end; padding: var(--s-3); opacity: 0; background: linear-gradient(0deg, rgba(5,7,13,.85), transparent 60%); transition: opacity var(--t-base); }
.sc:hover .sc__overlay, .sc:focus-visible .sc__overlay, .sc:focus-within .sc__overlay { opacity: 1; }
.sc__add { width: 100%; padding: 0.4375rem; border-radius: var(--r-sm); font-size: var(--fs-xs); font-weight: 600; color: #fff; background: var(--azure); transition: background var(--t-fast); }
.sc__add:hover { background: var(--azure-bright); }
.sc__add.is-in { background: transparent; color: var(--jade); border: 1px solid color-mix(in srgb, var(--jade) 35%, transparent); cursor: default; }

/* info below the cover — like the original seasonal card */
.sc__info { padding: var(--s-3) var(--s-1) 0; }
.sc__title { font-size: var(--fs-sm); font-weight: 600; line-height: var(--lh-snug); color: var(--ink); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.sc__meta { display: flex; gap: 0.3125rem; margin-top: 0.3125rem; font-size: var(--fs-xs); }
.sc__eps { color: var(--azure-bright); font-weight: 600; }
.sc__next { color: var(--ink-faint); }
/* Los géneros eran 3 píldoras POR TARJETA: 75 cápsulas en una pantalla de Temporada, todas del
   mismo peso que los controles reales. Como texto son dato, no interfaz. */
.sc__genres { margin-top: 0.375rem; font-size: var(--fs-2xs); color: var(--ink-faint); letter-spacing: .01em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sc__gtag { color: var(--ink-faint); }
</style>
