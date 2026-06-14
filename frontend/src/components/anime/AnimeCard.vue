<script setup>
import { computed } from 'vue'
import { ANIME_STATUS, animeFormatLabel } from '@/lib/anime'
import Icon from '@/components/ui/Icon.vue'

const props = defineProps({ anime: { type: Object, required: true } })
defineEmits(['open'])

const total = computed(() => props.anime.total_episodes || 0)
const done = computed(() => props.anime.downloaded_count || 0)
const status = computed(() => ANIME_STATUS[props.anime.status] || null)

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
      <img v-if="anime.cover" :src="anime.cover" :alt="anime.title" loading="lazy" class="acard__img"
           @load="$event.target.classList.add('is-loaded')" />
      <div class="acard__scrim" />
      <span class="acard__shine" />

      <span class="acard__fmt">{{ animeFormatLabel(anime.format) }}</span>
      <span v-if="status" class="acard__status" :style="{ '--c': status.color }">{{ status.label }}</span>

      <div class="acard__hover"><span class="acard__btn"><Icon name="play" :size="18" /></span></div>

      <!-- Title + (count left · dots right) overlaid on the poster bottom -->
      <div class="acard__overlay">
        <h3 class="acard__title">{{ anime.title }}</h3>
        <div class="acard__bottom">
          <span class="acard__eps">
            <span class="acard__count">{{ done }}</span><span v-if="total" class="acard__total"> / {{ total }}</span>
          </span>
          <div class="acard__dots">
            <span v-for="(d, i) in dots" :key="i" class="ad" :class="`ad--${d}`" />
          </div>
        </div>
      </div>
    </div>
  </article>
</template>

<style scoped>
.acard { cursor: pointer; outline: none; transition: transform var(--t-base) var(--ease-snap); }
.acard:hover, .acard:focus-visible { transform: translateY(-6px); }

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

.acard__hover { position: absolute; inset: 0; display: grid; place-items: center; opacity: 0; transition: opacity var(--t-base); }
.acard:hover .acard__hover, .acard:focus-visible .acard__hover { opacity: 1; }
.acard__btn {
  width: 50px; height: 50px; display: grid; place-items: center; border-radius: 50%;
  color: #fff; background: var(--azure); box-shadow: var(--glow-azure);
  transform: scale(.8); transition: transform var(--t-base) var(--ease-snap);
}
.acard:hover .acard__btn { transform: scale(1); }

.acard__overlay { position: absolute; left: 0; right: 0; bottom: 0; padding: var(--s-3); }
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
</style>
