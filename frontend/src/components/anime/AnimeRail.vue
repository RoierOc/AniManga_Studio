<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { imgProxy, imgThumb } from '@/lib/img'
import { animeFormatLabel } from '@/lib/anime'
import Icon from '@/components/ui/Icon.vue'

// Reusable Crunchyroll-style content rail. Two variants:
//  - 'episode' → 16:9 episode thumbnails (play action), optional pulsing NEW badge.
//  - 'poster'  → 2:3 cover cards (open/preview action), score badge.
// Each item is { anime, ep? }. The parent decides the action via @select.
const props = defineProps({
  title:    { type: String, required: true },
  items:    { type: Array, default: () => [] },
  variant:  { type: String, default: 'poster' },   // poster | episode
  newBadge: { type: Boolean, default: false },
})
defineEmits(['select'])

const epThumb = (it) => `/api/anime/thumb/${it.anime.id}/${it.ep?.num}`
const scoreOf = (a) => (a?.score && a.score > 0 ? Math.round(a.score) : null)

/* ── flechas de paginación (estilo Crunchyroll) ── */
const row = ref(null)
const canL = ref(false)
const canR = ref(false)
function updateArrows() {
  const el = row.value
  if (!el) { canL.value = false; canR.value = false; return }
  canL.value = el.scrollLeft > 8
  canR.value = el.scrollLeft + el.clientWidth < el.scrollWidth - 8
}
function page(dir) {
  row.value?.scrollBy({ left: dir * row.value.clientWidth * 0.9, behavior: 'smooth' })
}
onMounted(() => { updateArrows(); window.addEventListener('resize', updateArrows) })
onBeforeUnmount(() => window.removeEventListener('resize', updateArrows))
watch(() => props.items.length, async () => { await nextTick(); updateArrows() })
</script>

<template>
  <section v-if="items.length" class="rail">
    <div class="rail__head">
      <h3 class="rail__title">{{ title }}</h3>
      <div v-if="canL || canR" class="rail__nav">
        <button class="rail__arrow" :disabled="!canL" title="Anterior" @click="page(-1)">
          <Icon name="chevron" :size="16" :style="{ transform: 'rotate(180deg)' }" />
        </button>
        <button class="rail__arrow" :disabled="!canR" title="Siguiente" @click="page(1)">
          <Icon name="chevron" :size="16" />
        </button>
      </div>
    </div>

    <!-- Episode thumbnails (16:9) -->
    <div v-if="variant === 'episode'" ref="row" class="rail__row" @scroll.passive="updateArrows">
      <article v-for="it in items" :key="it.anime.id + '-' + (it.ep?.num ?? '')" class="ecard"
               @click="$emit('select', it)">
        <div class="ecard__thumb">
          <img :src="epThumb(it)" :alt="'Ep ' + it.ep?.num" loading="lazy"
               @load="$event.target.classList.add('is-loaded')"
               @error="$event.target.style.display = 'none'" class="ecard__img" />
          <div class="ecard__scrim" />
          <div class="ecard__play"><Icon name="play" :size="26" /></div>
          <span v-if="newBadge" class="ecard__new">NUEVO</span>
          <span class="ecard__ep">EP {{ it.ep?.num }}</span>
          <div v-if="it.ep?.resume_pos > 0 && it.ep?.duration" class="ecard__bar">
            <span :style="{ width: Math.min(100, it.ep.resume_pos / it.ep.duration * 100) + '%' }" />
          </div>
        </div>
        <div class="ecard__title">{{ it.anime.title }}</div>
        <div class="ecard__sub">Episodio {{ it.ep?.num }}</div>
      </article>
    </div>

    <!-- Poster cards (2:3) -->
    <div v-else ref="row" class="rail__row" @scroll.passive="updateArrows">
      <article v-for="it in items" :key="it.anime.id || it.anime.al_id" class="pcard"
               @click="$emit('select', it)">
        <div class="pcard__poster">
          <img v-if="imgThumb(it.anime.cover)" :src="imgThumb(it.anime.cover)" class="blurup" aria-hidden="true" alt="" />
          <img v-if="it.anime.cover" :src="imgProxy(it.anime.cover)" :alt="it.anime.title" loading="lazy"
               @load="$event.target.classList.add('is-loaded')" class="pcard__img" />
          <div v-else class="pcard__ph">{{ (it.anime.title || '?')[0].toUpperCase() }}</div>
          <div class="pcard__scrim" />
          <span class="pcard__fmt">{{ animeFormatLabel(it.anime.format) }}</span>
          <span v-if="scoreOf(it.anime)" class="pcard__score"><Icon name="heart" :size="10" /> {{ scoreOf(it.anime) }}</span>
          <div class="pcard__hover"><span class="pcard__btn"><Icon name="play" :size="16" /></span></div>
          <div class="pcard__overlay"><h4 class="pcard__name">{{ it.anime.title }}</h4></div>
        </div>
      </article>
    </div>
  </section>
</template>

<style scoped>
.rail { margin-bottom: var(--s-7); }
.rail__head { display: flex; align-items: center; justify-content: space-between; gap: var(--s-3); margin-bottom: var(--s-4); }
.rail__title { font-family: var(--font-display); font-size: var(--fs-xl); }
.rail__nav { display: flex; gap: var(--s-1); }
.rail__arrow {
  width: 2.25rem; height: 2.25rem; display: grid; place-items: center;
  border-radius: var(--r-sm); border: 1px solid var(--line); color: var(--ink-soft);
  background: var(--surface); transition: all var(--t-fast);
}
.rail__arrow:hover:not(:disabled) { color: var(--ink); border-color: var(--azure); background: var(--azure-haze); }
.rail__arrow:disabled { opacity: .3; cursor: default; }
.rail__row { display: flex; gap: var(--s-4); overflow-x: auto; padding-bottom: var(--s-3); scroll-snap-type: x mandatory; scroll-behavior: smooth; }
.rail__row::-webkit-scrollbar { height: 6px; }
.rail__row::-webkit-scrollbar-thumb { background: var(--line-2); border-radius: var(--r-pill); }

/* ── episode (16:9) ── */
.ecard { flex: 0 0 18.75rem; scroll-snap-align: start; cursor: pointer; transition: transform var(--t-base) var(--ease-snap); }
.ecard:hover { transform: translateY(-5px); }
.ecard__thumb { position: relative; aspect-ratio: 16/9; border-radius: var(--r-md); overflow: hidden; background: var(--surface-2); border: 1px solid var(--line); transition: box-shadow var(--t-base), border-color var(--t-base); }
.ecard:hover .ecard__thumb { border-color: var(--azure-glow); box-shadow: var(--shadow-lg); }
.ecard__img { width: 100%; height: 100%; object-fit: cover; opacity: 0; transition: opacity var(--t-slow); }
.ecard__img.is-loaded { opacity: 1; }
.ecard__scrim { position: absolute; inset: 0; background: linear-gradient(180deg, rgba(7,10,18,.1), rgba(5,7,13,.65)); }
.ecard__play { position: absolute; inset: 0; display: grid; place-items: center; color: #fff; opacity: 0; transition: opacity var(--t-base); }
.ecard:hover .ecard__play { opacity: 1; }
.ecard__play :deep(svg) { filter: drop-shadow(0 2px 8px rgba(0,0,0,.6)); }
.ecard__new { position: absolute; top: var(--s-2); right: var(--s-2); font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 700; letter-spacing: .04em; padding: 2px 7px; border-radius: var(--r-xs); color: #fff; background: var(--azure); box-shadow: var(--glow-azure); animation: pulse-live 1.8s var(--ease-drift) infinite; }
.ecard__ep { position: absolute; top: var(--s-2); left: var(--s-2); font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 700; padding: 2px 7px; border-radius: var(--r-xs); background: rgba(7,10,18,.7); color: var(--ice); }
.ecard__bar { position: absolute; left: 0; right: 0; bottom: 0; height: 3px; background: rgba(0,0,0,.4); }
.ecard__bar span { display: block; height: 100%; background: var(--azure-bright); box-shadow: 0 0 6px var(--azure-glow); }
.ecard__title { margin-top: var(--s-2); font-size: var(--fs-sm); font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: var(--ink); }
.ecard__sub { font-size: var(--fs-xs); color: var(--ink-faint); }

/* ── poster (2:3) ── */
.pcard { flex: 0 0 9.5rem; scroll-snap-align: start; cursor: pointer; transition: transform var(--t-base) var(--ease-snap); }
.pcard:hover { transform: translateY(-6px); }
.pcard__poster { position: relative; aspect-ratio: 2/3; border-radius: var(--r-md); overflow: hidden; background: var(--surface-2); border: 1px solid var(--line); box-shadow: var(--shadow-sm); transition: box-shadow var(--t-base), border-color var(--t-base); }
.pcard:hover .pcard__poster { border-color: var(--azure-glow); box-shadow: var(--shadow-lg), 0 0 0 1px var(--azure-glow); }
.pcard__img { width: 100%; height: 100%; object-fit: cover; opacity: 0; transition: opacity var(--t-slow), transform var(--t-cine) var(--ease-silk); }
.pcard__img.is-loaded { opacity: 1; }
.pcard:hover .pcard__img { transform: scale(1.07); }
.pcard__ph { position: absolute; inset: 0; display: grid; place-items: center; font-family: var(--font-display); font-size: 2.4rem; color: var(--ink-ghost); }
.pcard__scrim { position: absolute; inset: 0; background: linear-gradient(180deg, rgba(7,10,18,.25) 0%, transparent 30%, transparent 52%, rgba(5,7,13,.94) 100%); }
.pcard__fmt { position: absolute; top: var(--s-2); left: var(--s-2); font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 600; padding: 2px 6px; border-radius: var(--r-xs); color: var(--ink); background: rgba(7,10,18,.6); backdrop-filter: blur(6px); }
.pcard__score { position: absolute; top: var(--s-2); right: var(--s-2); display: inline-flex; align-items: center; gap: 3px; font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 700; padding: 2px 6px; border-radius: var(--r-pill); color: var(--gold); background: color-mix(in srgb, var(--gold) 15%, rgba(7,10,18,.7)); }
.pcard__hover { position: absolute; inset: 0; display: grid; place-items: center; opacity: 0; transition: opacity var(--t-base); }
.pcard:hover .pcard__hover { opacity: 1; }
.pcard__btn { width: 44px; height: 44px; display: grid; place-items: center; border-radius: 50%; color: #fff; background: var(--azure); box-shadow: var(--glow-azure); transform: scale(.8); transition: transform var(--t-base) var(--ease-snap); }
.pcard:hover .pcard__btn { transform: scale(1); }
.pcard__overlay { position: absolute; left: 0; right: 0; bottom: 0; padding: var(--s-3); }
.pcard__name { font-size: var(--fs-xs); font-weight: 600; line-height: var(--lh-snug); color: #fff; text-shadow: 0 1px 6px rgba(0,0,0,.65); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
</style>
