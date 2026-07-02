<script setup>
import { computed } from 'vue'
import { useAnimeStore } from '@/stores/anime'
import { ANIME_STATUS, animeFormatLabel, fmtCountdown } from '@/lib/anime'
import { imgProxy } from '@/lib/img'
import Icon from '@/components/ui/Icon.vue'

const props = defineProps({ anime: { type: Object, required: true } })
defineEmits(['open', 'play'])

const store = useAnimeStore()

const total = computed(() => props.anime.total_episodes || 0)
const done = computed(() => props.anime.downloaded_count || 0)
const status = computed(() => ANIME_STATUS[props.anime.status] || null)
const genres = computed(() => (props.anime.genres || []).slice(0, 3))
const synopsis = computed(() => props.anime.synopsis || '')

// Airing awareness — usa el mismo snapshot que el hero (store.airing keyed por al_id).
const airInfo = computed(() => store.airing[props.anime.al_id] || null)

// "NUEVO": el último episodio emitido ya está descargado y sin ver → listo para verse ya.
const hasNew = computed(() => {
  const inf = airInfo.value
  if (!inf?.last_episode) return false
  return (props.anime.episodes || []).some(e => e.num === inf.last_episode
    && (e.in_local || (e.in_qbt && e.progress >= 100)) && !e.watched)
})

// Cuenta regresiva al próximo episodio (series en emisión) — "Ep 5 · en 2d 3h".
const countdown = computed(() => {
  const inf = airInfo.value
  if (!inf?.next_airing_at || !inf?.next_episode) return null
  const c = fmtCountdown(inf.next_airing_at, store.nowSec)
  return c ? { ...c, episode: inf.next_episode } : null
})
const cdLabel = computed(() => {
  const c = countdown.value
  if (!c) return ''
  if (c.d > 0) return `${c.d}d ${c.h}h`
  if (c.h > 0) return `${c.h}h ${c.m}m`
  return `${c.m} min`
})

// First 12 episode availability dots — exactly like the original card:
// green = downloaded, yellow = downloading, faint = missing.
const dots = computed(() =>
  (props.anime.episodes || [])
    .filter(e => e.num > 0)
    .slice(0, 12)
    .map(e => {
      if (e.in_local || (e.in_qbt && e.progress >= 100)) return 'done'
      if (e.in_qbt && e.progress < 100) return 'dl'
      return 'missing'
    })
)
</script>

<template>
  <article class="acard" tabindex="0" @click="$emit('open', anime)" @keydown.enter="$emit('open', anime)">
    <div class="acard__poster">
      <img v-if="anime.cover" :src="imgProxy(anime.cover)" :alt="anime.title" loading="lazy" class="acard__img"
           @load="$event.target.classList.add('is-loaded')" />
      <div class="acard__scrim" />
      <span class="acard__shine" />

      <span class="acard__fmt">{{ animeFormatLabel(anime.format) }}</span>
      <span v-if="status" class="acard__status" :style="{ '--c': status.color }">{{ status.label }}</span>

      <!-- Title + extra info revealed on hover, all INSIDE the poster (no reflow, no overlap) -->
      <div class="acard__overlay">
        <!-- Airing awareness: "NUEVO" (episodio listo) tiene prioridad sobre la cuenta regresiva -->
        <span v-if="hasNew" class="acard__new"><span class="acard__newdot" /> NUEVO</span>
        <span v-else-if="countdown" class="acard__soon"><Icon name="clock" :size="11" /> Ep {{ countdown.episode }} · {{ cdLabel }}</span>
        <h3 class="acard__title">{{ anime.title }}</h3>
        <div class="acard__bottom">
          <span class="acard__eps">
            <span class="acard__count">{{ done }}</span><span v-if="total" class="acard__total"> / {{ total }}</span>
          </span>
          <div class="acard__dots">
            <span v-for="(d, i) in dots" :key="i" class="ad" :class="`ad--${d}`" />
          </div>
        </div>

        <!-- Hover-expand: crece hacia arriba dentro del póster (overflow:hidden lo recorta) -->
        <div class="acard__extra">
          <div v-if="genres.length" class="acard__genres">
            <span v-for="g in genres" :key="g" class="acard__g">{{ g }}</span>
          </div>
          <p v-if="synopsis" class="acard__syn">{{ synopsis }}</p>
          <div class="acard__acts">
            <button class="acard__act acard__act--play" @click.stop="$emit('play', anime)"><Icon name="play" :size="14" /> Ver</button>
            <button class="acard__act" @click.stop="$emit('open', anime)"><Icon name="spark" :size="13" /> Info</button>
          </div>
        </div>
      </div>
    </div>
  </article>
</template>

<style scoped>
.acard { position: relative; cursor: pointer; outline: none; transition: transform var(--t-base) var(--ease-snap); }
.acard:hover, .acard:focus-visible, .acard:focus-within { transform: translateY(-6px); z-index: 6; }

.acard__poster {
  position: relative; aspect-ratio: 2 / 3; border-radius: var(--r-md); overflow: hidden;
  background: var(--surface-2); border: 1px solid var(--line); box-shadow: var(--shadow-sm);
  transition: box-shadow var(--t-base) var(--ease-silk), border-color var(--t-base);
}
.acard:hover .acard__poster { border-color: var(--azure-glow); box-shadow: var(--shadow-lg), 0 0 0 1px var(--azure-glow); }

.acard__img { width: 100%; height: 100%; object-fit: cover; opacity: 0; transition: opacity var(--t-slow), transform var(--t-cine) var(--ease-silk); }
.acard__img.is-loaded { opacity: 1; }
.acard:hover .acard__img { transform: scale(1.07); }
.acard__scrim { position: absolute; inset: 0; background: linear-gradient(180deg, rgba(7,10,18,.4) 0%, transparent 26%, transparent 48%, rgba(5,7,13,.95) 100%); }
.acard__shine {
  position: absolute; inset: 0; pointer-events: none;
  background: linear-gradient(112deg, transparent 35%, rgba(168,200,255,.14) 48%, transparent 60%);
  transform: translateX(-120%);
}
.acard:hover .acard__shine { animation: shine .8s var(--ease-silk) forwards; }
@keyframes shine { to { transform: translateX(120%); } }

.acard__fmt {
  position: absolute; top: var(--s-2); left: var(--s-2);
  font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 600;
  padding: 2px 7px; border-radius: var(--r-xs); color: var(--ink);
  background: rgba(7,10,18,.6); backdrop-filter: blur(6px); letter-spacing: .03em;
}
.acard__status {
  position: absolute; top: var(--s-2); right: var(--s-2);
  font-size: var(--fs-2xs); font-weight: 600; padding: 2px 8px; border-radius: var(--r-pill);
  color: var(--c); background: color-mix(in srgb, var(--c) 16%, transparent);
  border: 1px solid color-mix(in srgb, var(--c) 40%, transparent); backdrop-filter: blur(6px);
}

.acard__overlay { position: absolute; left: 0; right: 0; bottom: 0; padding: var(--s-3);
  background: linear-gradient(180deg, transparent 0%, rgba(5,7,13,.5) 40%, rgba(5,7,13,.95) 100%); }
/* Airing badges — encima del título, sobre el scrim del póster */
.acard__new, .acard__soon {
  display: inline-flex; align-items: center; gap: 4px; margin-bottom: 5px;
  font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 700; letter-spacing: .03em;
  padding: 2px 8px; border-radius: var(--r-pill); backdrop-filter: blur(6px);
}
.acard__new { color: var(--jade); background: color-mix(in srgb, var(--jade) 20%, rgba(7,10,18,.6)); border: 1px solid color-mix(in srgb, var(--jade) 45%, transparent); }
.acard__newdot { width: 6px; height: 6px; border-radius: 50%; background: var(--jade); box-shadow: 0 0 6px var(--jade); animation: pulse-live 1.8s var(--ease-drift) infinite; }
.acard__soon { color: var(--ice); background: rgba(7,10,18,.6); border: 1px solid rgba(255,255,255,.16); }
.acard__soon :deep(svg) { color: var(--cyan); }
.acard__title {
  font-family: var(--font-body); font-weight: 600; font-size: var(--fs-sm); line-height: var(--lh-snug); color: #fff;
  text-shadow: 0 1px 6px rgba(0,0,0,.65);
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
/* count on the left · dots on the right — exactly like the original */
.acard__bottom { display: flex; align-items: center; justify-content: space-between; gap: 4px; margin-top: 6px; }
.acard__eps { font-size: var(--fs-xs); white-space: nowrap; text-shadow: 0 1px 4px rgba(0,0,0,.6); }
.acard__count { color: var(--azure-bright); font-weight: 700; }
.acard__total { color: var(--ink-soft); opacity: .7; }
.acard__dots { display: flex; flex-wrap: wrap; gap: 3px; justify-content: flex-end; }
.ad { width: 5px; height: 5px; border-radius: 50%; background: rgba(255,255,255,.16); }
.ad--done    { background: var(--azure-bright); }
.ad--dl      { background: var(--cyan); animation: pulse-live 1.6s var(--ease-drift) infinite; }
.ad--missing { background: rgba(255,255,255,.14); }

/* hover-expand: la info crece hacia arriba DENTRO del póster (overflow:hidden la recorta),
   sin desbordar ni tapar a las tarjetas vecinas. */
.acard__extra {
  max-height: 0; opacity: 0; overflow: hidden;
  transition: max-height var(--t-base) var(--ease-silk), opacity var(--t-base) var(--ease-silk), margin-top var(--t-base) var(--ease-silk);
}
.acard:hover .acard__extra, .acard:focus-within .acard__extra { max-height: 15rem; opacity: 1; margin-top: var(--s-2); }
.acard__genres { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: var(--s-2); }
.acard__g { font-size: var(--fs-2xs); padding: 2px 8px; border-radius: var(--r-pill);
  background: color-mix(in srgb, var(--azure) 28%, rgba(7,10,18,.5)); color: var(--ice); backdrop-filter: blur(4px); }
.acard__syn { font-size: var(--fs-xs); color: var(--ice); line-height: var(--lh-snug); margin-bottom: var(--s-3);
  text-shadow: 0 1px 4px rgba(0,0,0,.7);
  display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.acard__acts { display: flex; gap: var(--s-2); }
.acard__act { flex: 1; display: inline-flex; align-items: center; justify-content: center; gap: 5px;
  padding: var(--s-2); border-radius: var(--r-sm); font-size: var(--fs-xs); font-weight: 600;
  color: #fff; border: 1px solid rgba(255,255,255,.22); background: rgba(255,255,255,.08);
  backdrop-filter: blur(6px); transition: all var(--t-fast); }
.acard__act:hover { border-color: rgba(255,255,255,.5); background: rgba(255,255,255,.16); }
.acard__act--play { background: var(--azure); border-color: transparent; }
.acard__act--play:hover { background: var(--azure-bright); box-shadow: var(--glow-azure); }
</style>
